FROM maven:3.9.9-eclipse-temurin-17 AS build
WORKDIR /src
COPY pom.xml .
COPY src ./src
COPY conf ./conf
RUN mvn -q -DskipTests package \
 && cp target/taksitlio-stormcrawler-*-SNAPSHOT.jar /tmp/taksitlio-crawler.jar

FROM eclipse-temurin:17-jre
WORKDIR /topology
COPY --from=build /tmp/taksitlio-crawler.jar /topology/taksitlio-crawler.jar
COPY conf /topology/conf
# When used with compose profile "build", copy jar to shared volume.
CMD ["sh", "-c", "cp /topology/taksitlio-crawler.jar /out/taksitlio-crawler.jar && ls -la /out"]
