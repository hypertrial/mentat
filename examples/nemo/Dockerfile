FROM debian@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc AS toolchain
ARG NEMO_NIGHTLY=nightly-2026-08-12
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl libssl-dev pkg-config python3 \
    && rm -rf /var/lib/apt/lists/*
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo RUSTUP_TOOLCHAIN=${NEMO_NIGHTLY}
ENV PATH=/opt/cargo/bin:${PATH}
RUN curl -fsSLo /tmp/rustup-init \
      https://static.rust-lang.org/rustup/archive/1.28.2/aarch64-unknown-linux-gnu/rustup-init \
    && chmod +x /tmp/rustup-init \
    && /tmp/rustup-init -y --no-modify-path --profile minimal --default-toolchain "${NEMO_NIGHTLY}" \
    && rm /tmp/rustup-init \
    && chmod -R a+rX /opt/cargo /opt/rustup

FROM toolchain AS dependencies
WORKDIR /source
COPY . .
RUN mkdir -p /opt/mentat-cargo \
    && cargo vendor --locked --versioned-dirs /opt/mentat-cargo/vendor \
       > /opt/mentat-cargo/config.toml \
    && chmod -R a+rX /opt/mentat-cargo

FROM toolchain
COPY --from=dependencies /opt/mentat-cargo /opt/mentat-cargo
ENV CARGO_HOME=/tmp/mentat-cargo CARGO_NET_OFFLINE=true
