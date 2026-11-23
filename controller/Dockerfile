# docker CLI + Python. The controller restarts app containers through the mounted docker.sock.
FROM docker:27-cli
RUN apk add --no-cache python3
WORKDIR /srv
COPY . /srv/controller
EXPOSE 9100
CMD ["python3", "-m", "controller"]
