FROM golang:1.23-alpine AS build
WORKDIR /src
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/gateway .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates ffmpeg
WORKDIR /app
COPY --from=build /out/gateway /app/gateway
EXPOSE 8090
ENV GATEWAY_ADDR=:8090
ENTRYPOINT ["/app/gateway"]
