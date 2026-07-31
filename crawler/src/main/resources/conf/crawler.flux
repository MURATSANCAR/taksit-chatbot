name: "taksitlio-crawler"

includes:
  - resource: true
    file: "/crawler-default.yaml"
    override: false
  - resource: false
    file: "conf/crawler-conf.yaml"
    override: true

spouts:
  - id: "spout"
    className: "org.apache.stormcrawler.urlfrontier.Spout"
    parallelism: 1

bolts:
  - id: "partitioner"
    className: "org.apache.stormcrawler.bolt.URLPartitionerBolt"
    parallelism: 1
  - id: "fetcher"
    className: "org.apache.stormcrawler.bolt.FetcherBolt"
    parallelism: 1
  - id: "sitemap"
    className: "org.apache.stormcrawler.bolt.SiteMapParserBolt"
    parallelism: 1
  - id: "parse"
    className: "org.apache.stormcrawler.bolt.JSoupParserBolt"
    parallelism: 1
  - id: "index"
    className: "com.taksitlio.crawler.JsonFeedIndexerBolt"
    parallelism: 1
  - id: "status"
    className: "org.apache.stormcrawler.urlfrontier.StatusUpdaterBolt"
    parallelism: 1

streams:
  - from: "spout"
    to: "partitioner"
    grouping:
      type: SHUFFLE
  - from: "partitioner"
    to: "fetcher"
    grouping:
      type: FIELDS
      args: ["key"]
  - from: "fetcher"
    to: "sitemap"
    grouping:
      type: LOCAL_OR_SHUFFLE
  - from: "sitemap"
    to: "parse"
    grouping:
      type: LOCAL_OR_SHUFFLE
  - from: "parse"
    to: "index"
    grouping:
      type: LOCAL_OR_SHUFFLE
  - from: "fetcher"
    to: "status"
    grouping:
      type: FIELDS
      args: ["url"]
      streamId: "status"
  - from: "sitemap"
    to: "status"
    grouping:
      type: FIELDS
      args: ["url"]
      streamId: "status"
  - from: "parse"
    to: "status"
    grouping:
      type: FIELDS
      args: ["url"]
      streamId: "status"
  - from: "index"
    to: "status"
    grouping:
      type: FIELDS
      args: ["url"]
      streamId: "status"
