FROM alpine

ARG PYPI_SERVER_HOST
ARG PYPI_SERVER_SCHEME
ARG PYPI_SERVER_PORT

RUN apk update && \
    apk add \
        --no-cache \
        libpq \
        py-virtualenv \
        python && \
    virtualenv /venv && \
    mkdir -p /big_repos

COPY ./requirements.txt /
COPY ./run.py /

RUN source /venv/bin/activate && \
    pip install \
        --no-cache-dir \
        --trusted-host ${PYPI_SERVER_HOST} \
        --extra-index-url ${PYPI_SERVER_SCHEME}${PYPI_SERVER_HOST}:${PYPI_SERVER_PORT} \
        --requirement /requirements.txt

CMD ["/venv/bin/python", "-m", "run"]
