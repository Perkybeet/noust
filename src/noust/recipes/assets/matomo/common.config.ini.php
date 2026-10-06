; <?php exit; ?> DO NOT REMOVE THIS LINE
; Read by Matomo beside the config.ini.php its installer writes. Behind nginx, Matomo
; takes the visitor's address, the host and the scheme from the headers nginx sets,
; and not from the proxy's own connection.
[General]
proxy_client_headers[] = HTTP_X_FORWARDED_FOR
proxy_host_headers[] = HTTP_X_FORWARDED_HOST
proxy_scheme_headers[] = HTTP_X_FORWARDED_PROTO
