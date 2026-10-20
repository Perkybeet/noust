# A Rack application. Noust runs it with Puma on 127.0.0.1 and the port it chose.
run lambda { |_env|
  [200, { "content-type" => "text/plain; charset=utf-8" }, ["Hello from Noust (ruby)\n"]]
}
