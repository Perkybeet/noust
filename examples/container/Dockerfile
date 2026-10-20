# A container application: Noust builds this Dockerfile, keeps one image per release and runs it as a
# compose file it writes. The program listens on 8080 and prints one line.
FROM python:3.12-alpine
WORKDIR /srv
RUN printf 'Hello from Noust (container)\n' > index.html
EXPOSE 8080
CMD ["python", "-m", "http.server", "8080", "--bind", "0.0.0.0"]
