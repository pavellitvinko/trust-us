# Holds the probe's network namespace and blocks everything except the public internet.
FROM alpine:3.20
RUN apk add --no-cache iptables
COPY guard.sh /guard.sh
RUN chmod 0555 /guard.sh
ENTRYPOINT ["/guard.sh"]
