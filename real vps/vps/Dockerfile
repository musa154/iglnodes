# vps/Dockerfile
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    openssh-server sudo curl pwgen tmate \
    && mkdir -p /var/run/sshd

COPY setup.sh /usr/local/bin/setup.sh
RUN chmod +x /usr/local/bin/setup.sh

EXPOSE 22

CMD ["/usr/local/bin/setup.sh"]
