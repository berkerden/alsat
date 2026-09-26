# Sunucu (VPS) görüntüsü (Faz 7). Mac'te kullanılmaz; Mac'te kurulum.sh çalışır.
#
# Üç aşama:
#   temel    : Python ve kilitli bağımlılıklar (requirements.lock, özetli).
#   sinama   : temel + test bağımlılıkları + testler. Derlenirken testler
#              çalışır; geçmezse görüntü çıkmaz.
#   calisma  : temel + yalnızca uygulama kodu, root olmayan kullanıcıyla.
#
# Taban görüntü sürüm ve özetle sabit: her derleme aynı temelden çıkar
# (SPEC §5 "Bağımlılık sürümleri sabitlensin"). Özet çok mimarili listeyi
# gösterir; hem x86 hem ARM sunucuda çalışır.

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS temel

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/uygulama/src

WORKDIR /uygulama
COPY requirements.lock ./
# --require-hashes: kilitteki özetle tutmayan paket kurulmaz.
RUN pip install --require-hashes -r requirements.lock
# Paket kurulmaz, kaynak PYTHONPATH'ten okunur: kurmak için derleme aracı
# (hatchling) indirmek gerekirdi ve o kilitte değil.
COPY src ./src


FROM temel AS sinama
COPY requirements-dev.lock ./
RUN pip install --require-hashes -r requirements-dev.lock
COPY config ./config
COPY tests ./tests
COPY pyproject.toml ./
RUN python -m pytest -q -p no:cacheprovider


FROM temel AS calisma
# Uygulama root olarak çalışmaz. Kimlik 10001 sabit: sunucudaki veri ve sır
# dizinlerinin sahibi bu kimliğe göre ayarlanıyor (sunucu/ilk-kurulum.sh).
RUN useradd --uid 10001 --user-group --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin albsat \
    && mkdir -p /veri /sirlar \
    && chown albsat:albsat /veri /sirlar \
    && chmod 700 /sirlar
USER albsat
# Ortam değişkeni yalnızca sırların YERİNİ söyler; sırrın kendisi dosyada.
ENV ALBSAT_SIR_DIZINI=/sirlar
VOLUME ["/veri"]
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
    CMD ["python", "-m", "albsat.cli.yoklama", "--sessiz"]
CMD ["python", "-m", "albsat.cli.serve", "--veri-dizini", "/veri", "--tarayici-acma", "--sabit-port"]
