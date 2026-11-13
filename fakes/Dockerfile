# One image for both fakes. docker-compose picks which one runs with the command.
FROM python:3.12-slim

WORKDIR /srv
COPY fakes/requirements.txt fakes/requirements.txt
RUN pip install --no-cache-dir -r fakes/requirements.txt
COPY fakes/ fakes/

EXPOSE 8000
CMD ["uvicorn", "fakes.fake_app:app", "--host", "0.0.0.0", "--port", "8000"]
