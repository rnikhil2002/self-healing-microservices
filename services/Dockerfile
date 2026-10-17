# Builds either service from the multi-module project:
#   docker build --build-arg SERVICE=order-service -t order-service services/
FROM maven:3.9-eclipse-temurin-17 AS build
ARG SERVICE
WORKDIR /app
COPY pom.xml .
COPY chaos-kit/pom.xml chaos-kit/
COPY order-service/pom.xml order-service/
COPY payment-service/pom.xml payment-service/
RUN mvn -q -B -pl ${SERVICE} -am dependency:go-offline || true
COPY . .
RUN mvn -q -B -pl ${SERVICE} -am package -DskipTests && cp ${SERVICE}/target/${SERVICE}-*.jar /app/app.jar

FROM eclipse-temurin:17-jre
WORKDIR /app
RUN useradd --system --uid 1001 app
COPY --from=build /app/app.jar app.jar
USER app
ENV JAVA_OPTS="-Xmx256m"
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/app.jar"]
