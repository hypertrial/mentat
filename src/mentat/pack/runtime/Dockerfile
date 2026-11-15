FROM python@sha256:df540bfd30bd380e89a349611ce42aa67bcb542d58c047706616eb6ba2f86a1b
COPY requirements.txt /opt/requirements.txt
RUN python -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: --target /opt/polaris -r /opt/requirements.txt \
    && rm -rf /opt/polaris/apache_polaris/cli \
    && mkdir /opt/polaris/apache_polaris/cli
ENV PYTHONPATH=/opt/polaris PYTHONDONTWRITEBYTECODE=1
