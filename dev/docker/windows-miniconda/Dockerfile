# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

FROM mcr.microsoft.com/windows/servercore:ltsc2019
WORKDIR C:/Users/Administrator
RUN powershell -Command " \
    $url = \"https://repo.anaconda.com/miniconda/Miniconda3-py39_4.12.0-Windows-x86_64.exe\"; \
    $client = new-object System.Net.WebClient; \
    $client.DownloadFile( $url, \"miniconda3.exe\"); \
    ./miniconda3.exe /S /D=C:\Users\Administrator\miniconda3 | Write-Output; \
    del miniconda3.exe; \
    "

ENV PATH=C:\\Windows\\system32;C:\\Windows;C:\Windows\\System32\\Wbem;C:\\Windows\\System32\\WindowsPowerShell\\v1.0;C:\\Users\\Administrator\\miniconda3;C:\\Users\\Administrator\\miniconda3\\Scripts;C:\\Users\\Administrator\\miniconda3\\Library\\bin

RUN conda config --set always_yes true
RUN pip install pip-tools

ENTRYPOINT ["powershell.exe"]
