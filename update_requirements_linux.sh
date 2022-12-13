#! /bin/sh
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.


docker-compose -f dev/docker-compose.yml run generate-python3.7-requirements
docker-compose -f dev/docker-compose.yml run generate-python3.8-requirements
docker-compose -f dev/docker-compose.yml run generate-python3.9-requirements
