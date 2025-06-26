#!/bin/bash

if [ "`echo ${HOSTNAME} | cut -d - -f 3`" == "standby" ]; then
    echo "This is a standby server, initialization required."
    if [ ! -f ${PGDATA}/standby.signal ]; then
        distro=$(echo "${HOSTNAME}" | cut -d - -f 1)
        version=$(echo "${HOSTNAME}" | cut -d - -f 2 | cut -b 3-)
        echo "Detected Distro: ${distro}, PostgreSQL Version: ${version}"
        case "${distro}" in
            ubuntu*)
                PGDATA="/var/lib/postgresql/${version}/main"
                echo "Stopping PostgreSQL service..."
                sudo systemctl stop postgresql
                ;;
            rocky*)
                PGDATA="/var/lib/pgsql/${version}/data"
                echo "Stopping PostgreSQL service..."
                sudo systemctl stop postgresql-${version}
                ;;
            *)
                echo "Unrecognized distro ${distro}, cannot stop PostgreSQL service."
                exit 1
                ;;
        esac
        echo "Waiting for PostgreSQL service to stop..."
        sleep 3
        echo "PGDATA directory: ${PGDATA}"
        if [ -d "${PGDATA}" ]; then
            echo "Removing old data directory..."
            rm -rf ${PGDATA}
        fi
        echo "Creating new data directory..."
        mkdir -p ${PGDATA}
        chmod 700 ${PGDATA}
        echo "Running pg_basebackup to initialize standby server..."
        echo "Using replication slot 'replslot' for base backup."
        PGPASSWORD=repluser pg_basebackup -h "`echo ${HOSTNAME} | cut -d - -f 1-2`-active" -D "${PGDATA}" -U repluser -P -v -R -X stream -C -S replslot
        while [ "$?" != "0" ]; do
            echo "pg_basebackup failed, retrying in 3 seconds..."
            sleep 3
            if [ -d "${PGDATA}" ]; then
                echo "Removing existing data directory..."
                rm -rf ${PGDATA}
                echo "Creating new data directory..."
                mkdir -p ${PGDATA}
                chmod 700 ${PGDATA}
            fi
            PGPASSWORD=repluser pg_basebackup -h "`echo ${HOSTNAME} | cut -d - -f 1-2`-active" -D "${PGDATA}" -U repluser -P -v -R -X stream -C -S replslot
        done
        echo "PostgreSQL standby server initialized successfully."
        touch ${PGDATA}/standby.signal
        case "${distro}" in
            ubuntu*)
                echo "Starting PostgreSQL service..."
                sudo systemctl start postgresql
                ;;
            rocky*)
                echo "Starting PostgreSQL service..."
                sudo systemctl start "postgresql-${version}"
                ;;
        esac
    fi
    echo "Initialization complete."
else
    echo "This is an `echo ${HOSTNAME} | cut -d - -f 3` server, no initialization required."
fi

echo "Removing initialization script and crontab..."
rm -f ${HOME}/initialize.sh
crontab -r

exit 0
