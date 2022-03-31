#! /bin/sh

docker-compose -f dev/docker-compose.yml run generate-python3.7-requirements
docker-compose -f dev/docker-compose.yml run generate-python3.8-requirements
docker-compose -f dev/docker-compose.yml run generate-python3.9-requirements
