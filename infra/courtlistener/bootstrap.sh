#!/bin/sh
# Runs as a Job on every deploy, before web and celery roll out. Everything
# in here is safe to repeat.
set -eu
cd /opt/courtlistener

# collectstatic first: migrate runs Django's system checks, which import the
# URL conf, which resolves static() URLs through the manifest storage. That
# needs the manifest and files to already be in the bucket. collectstatic
# itself runs no system checks.
echo "==> collectstatic (to Spaces)"
./manage.py collectstatic --noinput

echo "==> migrate"
./manage.py migrate --noinput
./manage.py createcachetable

echo "==> seed users, the MCP OAuth application, and sample data"
python /opt/flp-dev/seed.py

echo "==> elasticsearch indices"
# Create the indices if they do not exist yet, then queue indexing of the
# sample data for the celery worker (which starts after this job).
./manage.py search_index --create || echo "indices already exist or creation failed; continuing"
for search_type in o r p oa; do
    ./manage.py cl_index_parent_and_child_docs \
        --search-type "$search_type" --queue celery \
        || echo "indexing $search_type failed; continuing"
done

echo "==> done"
