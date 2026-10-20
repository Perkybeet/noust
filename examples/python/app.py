# A WSGI application. Noust runs it with Gunicorn on 127.0.0.1 and the port it chose.
def app(environ, start_response):
    body = b"Hello from Noust (python)\n"
    start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))])
    return [body]
