
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV MPLBACKEND=Agg

# Install native geospatial libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gdal-bin \
    libgdal-dev \
    libproj-dev \
    proj-data \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# Install Python dependencies and matching GDAL bindings
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip install --no-cache-dir --no-build-isolation \
       "GDAL[numpy]==$(gdal-config --version)" \
    && python -c "from osgeo import gdal, ogr, osr, gdal_array; import numpy; print('GDAL:', gdal.VersionInfo())"

COPY . .

RUN mkdir -p outputs

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 1 --timeout 1800 app:app"]
