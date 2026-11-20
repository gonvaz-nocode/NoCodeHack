FROM python:3.13-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
RUN pip install --no-cache-dir numpy==2.5.3 scipy==1.18.1
WORKDIR /bot
COPY mechfight.py mapprep.py geo.py bot.py ./
COPY arena ./arena
RUN python3 mapprep.py arena map.npz && python3 -c "import geo; geo.Geo('arena')" && python3 -m compileall -q /bot
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
