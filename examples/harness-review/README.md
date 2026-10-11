# Run the example

## For Humans

With Docker running, run these commands from the repository root:

```sh
export MINIMAX_API_KEY="your-minimax-api-key"
docker build -t anyo:local .
docker run --rm -e MINIMAX_API_KEY -e HARNESS=omo anyo:local
```

For OpenCode, replace `HARNESS=omo` with `HARNESS=opencode`.
