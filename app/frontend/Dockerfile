# ==============================================================================
# PRODUCTION MULTI-STAGE DOCKERFILE: Frontend Dashboard
# ==============================================================================
# Ultra-lightweight (~23 MB) Alpine NGINX web server & reverse proxy
# ==============================================================================

FROM nginx:1.25-alpine

# Remove default static files and config
RUN rm -rf /usr/share/nginx/html/* /etc/nginx/conf.d/default.conf

# Copy custom reverse proxy & caching configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy application dashboard assets
COPY index.html /usr/share/nginx/html/

# Harden container file permissions
RUN chown -R nginx:nginx /usr/share/nginx/html && \
    chmod -R 755 /usr/share/nginx/html && \
    chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d && \
    touch /var/run/nginx.pid && \
    chown -R nginx:nginx /var/run/nginx.pid

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:80/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
