FROM python:3.11-slim

RUN pip install --no-cache-dir gingugu

CMD ["gingugu"]
