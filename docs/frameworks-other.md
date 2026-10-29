# Stacks without a native runtime

Part of [Deploying by framework](frameworks.md).

Go, Rust, Java and Kotlin, .NET and Deno are built on the server
([build.md](build.md#java-and-kotlin-net-elixir-and-deno)) unless the repository commits a
`Dockerfile`, like [Ruby and Elixir](frameworks-ruby-elixir-static.md). The rest (Swift, Zig, Crystal...), and any of those you want
packaged, run as **container applications**: from your `Dockerfile`, or without one with Railpack.

```bash
noust create -d api.example.com -s https://github.com/you/api.git -t container
noust setup railpack                      # once, for repositories with no Dockerfile
noust create -d api.example.com -s https://github.com/you/api.git -t container --builder railpack
```

Each release is an image, so rollback is as quick as for any other application
([containers.md](containers.md)). The migration is a `pre_deploy` hook that runs in a one-off
container of the new image (`bin/rails db:migrate`, `mix ecto.migrate`).

A static site generator whose tools the server has (Eleventy and Docusaurus need only Node) is a
`static` application with `install`, `build` and `publish_directory` in its `build:` block; one whose
toolchain the server does not have is built where the toolchain is, as the Jekyll section above says.
