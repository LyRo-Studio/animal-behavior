# Config-only image: the reverse proxy in front of frontend and backend
# (ticket #71). Built and pushed by deploy.yml like the other images, so a
# rollback tag carries the nginx config that matched it.
FROM nginx:1.27-alpine
COPY default.conf /etc/nginx/conf.d/default.conf
