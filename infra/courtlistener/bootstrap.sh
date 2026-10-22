#!/bin/sh
# Runs as a Job on every deploy of a slug, before its web and celery roll
# out. Everything in here is safe to repeat. SEED_DEV_DATA, INDEX_SEARCH and
# extra.sh come from the slug's entry in deploys.py.
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

echo "==> seed users, the MCP OAuth applications, and sample data (SEED_DEV_DATA=${SEED_DEV_DATA:-on})"
python /opt/flp-dev/seed.py

if [ "${INDEX_SEARCH:-on}" = "on" ]; then
    echo "==> elasticsearch indices"
    # Create the indices if they do not exist yet, then queue indexing for
    # the celery worker (which starts after this job).
    ./manage.py search_index --create || echo "indices already exist or creation failed; continuing"
    for search_type in o r p oa; do
        ./manage.py cl_index_parent_and_child_docs \
            --search-type "$search_type" --queue celery \
            || echo "indexing $search_type failed; continuing"
    done
else
    echo "==> elasticsearch indexing skipped (INDEX_SEARCH=off)"
fi

echo "==> extra commands for this slug"
sh /opt/flp-dev/extra.sh

echo "==> done"
