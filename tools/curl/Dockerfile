ARG TAG=3.12.3
FROM alpine:${TAG}
RUN apk add --update --no-cache curl rsync
RUN curl -L https://github.com/tianon/gosu/releases/download/1.12/gosu-amd64 -o /usr/local/bin/gosu && chmod +x /usr/local/bin/gosu

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
WORKDIR /download
ENTRYPOINT [ "/usr/local/bin/entrypoint.sh" ]
