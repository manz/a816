# Python usage

```python
from a816.writers import IPSWriter
from a816.program import Program

if __name__ == "__main__":
    p = Program()
    
    p.resolver.current_scope.add_symbol("external_symbol", "0xDE0134")
    
    with open("patch.ips", "wb") as f:
        ips_writer = IPSWriter(f)
        p.assemble_string_with_emitter("lda.l external_symbol", "test.s", ips_writer)
```
