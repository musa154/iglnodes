#!/bin/bash
set -e

# Vars (allow override by env)
USER_NAME="${USER_NAME:-vpsuser}"
USER_PASS="${USER_PASS:-password123}"
ROOT_PASS="${ROOT_PASS:-root123}"

echo "[SETUP] Creating user ${USER_NAME}"

# create user if not exists
if ! id -u "$USER_NAME" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$USER_NAME"
fi

# set passwords
echo "${USER_NAME}:${USER_PASS}" | chpasswd
echo "root:${ROOT_PASS}" | chpasswd

# optional sudo without password
if ! grep -q "^${USER_NAME} " /etc/sudoers; then
  echo "${USER_NAME} ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers
fi

# start sshd
service ssh start || /usr/sbin/sshd

# start tmate session in background and write its SSH access string
# start detached session and output tmate_ssh to file
# attempt retries because tmate may take time
tmate -F -S /tmp/tmate.sock new-session -d
tries=0
while [ $tries -lt 10 ]; do
  if tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}' >/dev/null 2>&1; then
    break
  fi
  tries=$((tries+1))
  sleep 1
done

if tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}' >/dev/null 2>&1; then
  TMATE_SSH=$(tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}')
  echo "$TMATE_SSH" > /tmate_session.txt
  echo "[SETUP] tmate session created: $TMATE_SSH"
else
  echo "[SETUP] tmate not available"
  echo "Not available" > /tmate_session.txt
fi

# keep container alive
tail -f /dev/null
