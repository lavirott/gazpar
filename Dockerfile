FROM python:3.9.10-alpine3.15

ENV DEV_PACKAGES="\
    build-base \
    libxml2-dev \
    libxslt-dev \
"
WORKDIR /app

COPY requirements.txt ./

RUN apk add --no-cache --virtual build-deps $DEV_PACKAGES \
    && python -m pip install --upgrade pip \
    && export MAKEFLAGS="-j$(nproc)" \
    && python3 -m pip install --no-cache-dir -r requirements.txt \
    && apk del build-deps

COPY *.py ./
COPY .params ./

ENTRYPOINT ["python3"]
CMD ["gazinflux.py", "--days", "5"]
