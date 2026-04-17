FROM python:3.11-slim
RUN apt-get update \
&& apt-get -y install gettext \
&& apt-get clean \
&& rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN ./compile_locales.sh

CMD ["python3", "BombayBot.py"]
