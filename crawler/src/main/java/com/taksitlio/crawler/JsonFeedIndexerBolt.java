package com.taksitlio.crawler;

import static org.apache.stormcrawler.Constants.StatusStreamName;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.apache.commons.lang3.StringUtils;
import org.apache.storm.task.OutputCollector;
import org.apache.storm.task.TopologyContext;
import org.apache.storm.tuple.Tuple;
import org.apache.storm.tuple.Values;
import org.apache.stormcrawler.Metadata;
import org.apache.stormcrawler.indexing.AbstractIndexerBolt;
import org.apache.stormcrawler.persistence.Status;
import org.apache.stormcrawler.util.ConfUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Writes ADR-010 JSON feeds under {@code taksitlio.feed.dir}.
 *
 * <p>Product feeds: {@code {source_code}.json} with {@code {"products":[...]}}. Campaign feeds:
 * {@code {source_code}.json} with {@code {"campaigns":[...]}}.
 *
 * <p>Never invents price, stock, or rates — missing required fields skip the row.
 */
public class JsonFeedIndexerBolt extends AbstractIndexerBolt {

  private static final Logger LOG = LoggerFactory.getLogger(JsonFeedIndexerBolt.class);
  private static final ObjectMapper MAPPER = new ObjectMapper();

  private OutputCollector collector;
  private Path feedDir;
  private int flushEvery;

  /** source_code → in-memory product/campaign rows keyed by id */
  private final Map<String, Map<String, ObjectNode>> productBuffers = new ConcurrentHashMap<>();

  private final Map<String, Map<String, ObjectNode>> campaignBuffers = new ConcurrentHashMap<>();
  private final Map<String, Integer> dirtyCounts = new ConcurrentHashMap<>();

  @Override
  public void prepare(
      Map<String, Object> conf, TopologyContext context, OutputCollector collector) {
    super.prepare(conf, context, collector);
    this.collector = collector;
    this.feedDir = Path.of(ConfUtils.getString(conf, "taksitlio.feed.dir", "/feeds"));
    this.flushEvery = ConfUtils.getInt(conf, "taksitlio.feed.flush.every", 25);
    try {
      Files.createDirectories(feedDir);
    } catch (IOException e) {
      throw new RuntimeException("Cannot create feed dir " + feedDir, e);
    }
  }

  @Override
  public void execute(Tuple tuple) {
    String url = tuple.getStringByField("url");
    Metadata metadata = (Metadata) tuple.getValueByField("metadata");

    if (!filterDocument(metadata)) {
      collector.emit(StatusStreamName, tuple, new Values(url, metadata, Status.FETCHED));
      collector.ack(tuple);
      return;
    }

    String sourceCode = first(metadata, "taksitlio.source_code", "source_code");
    String channel =
        first(metadata, "taksitlio.channel", "channel");
    if (StringUtils.isBlank(channel)) {
      channel = guessChannel(url, metadata);
    }
    if (StringUtils.isBlank(sourceCode)) {
      sourceCode = "unmapped";
      LOG.warn("Missing taksitlio.source_code for {}; writing to unmapped", url);
    }

    try {
      if ("CAMPAIGN".equalsIgnoreCase(channel)) {
        ObjectNode row = buildCampaign(url, metadata);
        if (row != null) {
          campaignBuffers
              .computeIfAbsent(sourceCode, k -> new ConcurrentHashMap<>())
              .put(row.get("id").asText(), row);
          maybeFlush(sourceCode, true);
        } else {
          LOG.debug("Skip campaign (incomplete): {}", url);
        }
      } else {
        ObjectNode row = buildProduct(url, metadata);
        if (row != null) {
          productBuffers
              .computeIfAbsent(sourceCode, k -> new ConcurrentHashMap<>())
              .put(row.get("id").asText(), row);
          maybeFlush(sourceCode, false);
        } else {
          LOG.debug("Skip product (incomplete / no invent): {}", url);
        }
      }
    } catch (Exception e) {
      LOG.error("Index failed for {}", url, e);
      collector.emit(StatusStreamName, tuple, new Values(url, metadata, Status.ERROR));
      collector.ack(tuple);
      return;
    }

    collector.emit(StatusStreamName, tuple, new Values(url, metadata, Status.FETCHED));
    collector.ack(tuple);
  }

  private void maybeFlush(String sourceCode, boolean campaign) throws IOException {
    int n = dirtyCounts.merge(sourceCode, 1, Integer::sum);
    if (n >= flushEvery) {
      flushSource(sourceCode, campaign);
      dirtyCounts.put(sourceCode, 0);
    }
  }

  @Override
  public void cleanup() {
    try {
      for (String code : productBuffers.keySet()) {
        flushSource(code, false);
      }
      for (String code : campaignBuffers.keySet()) {
        flushSource(code, true);
      }
    } catch (IOException e) {
      LOG.error("Flush on cleanup failed", e);
    }
  }

  private synchronized void flushSource(String sourceCode, boolean campaign) throws IOException {
    Map<String, ObjectNode> buf =
        campaign ? campaignBuffers.get(sourceCode) : productBuffers.get(sourceCode);
    if (buf == null || buf.isEmpty()) {
      return;
    }
    ObjectNode root = MAPPER.createObjectNode();
    ArrayNode arr = root.putArray(campaign ? "campaigns" : "products");
    for (ObjectNode row : buf.values()) {
      arr.add(row);
    }
    Path target = feedDir.resolve(sourceCode + ".json");
    Path tmp = feedDir.resolve(sourceCode + ".json.tmp");
    Files.writeString(tmp, MAPPER.writerWithDefaultPrettyPrinter().writeValueAsString(root), StandardCharsets.UTF_8);
    Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    LOG.info("Flushed {} rows to {}", arr.size(), target);
  }

  private static ObjectNode buildProduct(String url, Metadata md) {
    String name = first(md, "name", "title", "parse.title");
    String priceRaw = first(md, "price", "offers.price");
    if (StringUtils.isBlank(name) || StringUtils.isBlank(priceRaw)) {
      // Do not invent name or price.
      return null;
    }
    Double price = parseDouble(priceRaw);
    if (price == null) {
      return null;
    }
    String id =
        first(md, "sku", "gtin", "product_id");
    if (StringUtils.isBlank(id)) {
      id = Integer.toHexString(url.hashCode());
    }
    ObjectNode row = MAPPER.createObjectNode();
    row.put("id", id);
    row.put("name", name.trim());
    putIf(row, "sku", first(md, "sku"));
    putIf(row, "gtin", first(md, "gtin", "gtin13", "gtin14"));
    putIf(row, "ean", first(md, "ean"));
    putIf(row, "mpn", first(md, "mpn"));
    putIf(row, "brand", first(md, "brand", "brand.name"));
    putIf(row, "model", first(md, "model", "model_number"));
    row.put("url", url);
    row.put("price", price);
    Double list = parseDouble(first(md, "list_price", "offers.highPrice"));
    if (list != null) {
      row.put("list_price", list);
    }
    String currency = first(md, "currency", "priceCurrency", "offers.priceCurrency");
    row.put("currency", StringUtils.isBlank(currency) ? "TRY" : currency);
    row.put("stock_status", mapAvailability(first(md, "availability", "offers.availability")));
    putIf(row, "image_url", firstImage(md));
    putIf(row, "category", first(md, "category"));
    return row;
  }

  private static ObjectNode buildCampaign(String url, Metadata md) {
    String name = first(md, "name", "title", "parse.title", "campaign_name");
    if (StringUtils.isBlank(name)) {
      return null;
    }
    String institution = first(md, "taksitlio.institution_code", "institution_code");
    if (StringUtils.isBlank(institution)) {
      return null;
    }
    String id = first(md, "campaign_id", "id");
    if (StringUtils.isBlank(id)) {
      id = Integer.toHexString(url.hashCode());
    }
    ObjectNode row = MAPPER.createObjectNode();
    row.put("id", id);
    row.put("institution_code", institution);
    row.put("name", name.trim());
    String type = first(md, "campaign_type");
    row.put("campaign_type", StringUtils.isBlank(type) ? "INSTALLMENT" : type);
    putIf(row, "summary", first(md, "summary", "description"));
    putIf(row, "valid_from", first(md, "valid_from"));
    putIf(row, "valid_until", first(md, "valid_until"));
    Double min = parseDouble(first(md, "min_amount", "minimum_purchase_amount"));
    Double max = parseDouble(first(md, "max_amount", "maximum_purchase_amount"));
    if (min != null) {
      row.put("min_amount", min);
    }
    if (max != null) {
      row.put("max_amount", max);
    }
    // Terms/rates only if explicitly present — never invent.
    String months = first(md, "term_months");
    String rate = first(md, "rate_apr", "annual_cost_rate");
    if (StringUtils.isNotBlank(months) || StringUtils.isNotBlank(rate)) {
      ArrayNode terms = row.putArray("terms");
      ObjectNode term = terms.addObject();
      Integer m = parseInt(months);
      if (m != null) {
        term.put("months", m);
      }
      Double r = parseDouble(rate);
      if (r != null) {
        term.put("rate_apr", r);
      }
      Double fee = parseDouble(first(md, "fee"));
      if (fee != null) {
        term.put("fee", fee);
      }
    } else {
      row.putArray("terms");
    }
    row.putArray("merchant_codes");
    row.putArray("category_codes");
    row.put("source_url", url);
    return row;
  }

  private static String guessChannel(String url, Metadata md) {
    String u = url.toLowerCase(Locale.ROOT);
    if (u.contains("kampanya")
        || u.contains("campaign")
        || u.contains("taksit")
        || u.contains("installment")
        || "CAMPAIGN".equalsIgnoreCase(first(md, "page_type"))) {
      return "CAMPAIGN";
    }
    if (StringUtils.isNotBlank(first(md, "price", "sku", "gtin", "offers.price"))) {
      return "PRODUCT";
    }
    return "PRODUCT";
  }

  private static String mapAvailability(String raw) {
    if (StringUtils.isBlank(raw)) {
      return "UNKNOWN";
    }
    String v = raw.toLowerCase(Locale.ROOT);
    if (v.contains("instock") || v.contains("in_stock") || v.contains("available")) {
      return "AVAILABLE";
    }
    if (v.contains("outofstock") || v.contains("out_of_stock") || v.contains("sold")) {
      return "OUT_OF_STOCK";
    }
    if (v.contains("limited")) {
      return "LIMITED";
    }
    return "UNKNOWN";
  }

  private static String firstImage(Metadata md) {
    String img = first(md, "image", "image_url", "og:image");
    if (StringUtils.isBlank(img)) {
      return null;
    }
    // JSON-LD may yield array-like strings; take first URL token.
    if (img.startsWith("[")) {
      int http = img.indexOf("http");
      if (http >= 0) {
        int end = img.indexOf('"', http);
        if (end > http) {
          return img.substring(http, end);
        }
      }
    }
    return img;
  }

  private static void putIf(ObjectNode row, String key, String value) {
    if (StringUtils.isNotBlank(value)) {
      row.put(key, value.trim());
    }
  }

  private static String first(Metadata md, String... keys) {
    for (String key : keys) {
      String v = md.getFirstValue(key);
      if (StringUtils.isNotBlank(v)) {
        return v;
      }
    }
    return null;
  }

  private static Double parseDouble(String raw) {
    if (StringUtils.isBlank(raw)) {
      return null;
    }
    try {
      String cleaned = raw.replace("₺", "").replace("TRY", "").replace(",", "").trim();
      return Double.parseDouble(cleaned);
    } catch (NumberFormatException e) {
      return null;
    }
  }

  private static Integer parseInt(String raw) {
    if (StringUtils.isBlank(raw)) {
      return null;
    }
    try {
      return Integer.parseInt(raw.trim());
    } catch (NumberFormatException e) {
      return null;
    }
  }
}
