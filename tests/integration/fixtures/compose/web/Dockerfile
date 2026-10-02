# A few megabytes of Python and one file: the whole stack builds in seconds, and the
# image a scenario "updates" to is whatever VERSION and DELAY say in the commit.
FROM python:3.12-alpine
WORKDIR /app
COPY . /app
CMD ["python", "-u", "/app/server.py"]
