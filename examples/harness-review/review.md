# Review an invoice function

## For Humans

An empty invoice should have an average price of zero. Review this function and explain how to fix it.

```python
def average_price(prices):
    return sum(prices) / len(prices)
```

What happens when `prices` is empty? Put a JSON object in the result's `data` field with `error` (the exception class name) and `fix` (one sentence). Use only the code above; do not read or change files.
