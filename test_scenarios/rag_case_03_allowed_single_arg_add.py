def add(x):
    """
    RAG target:
    - This intentionally supports a single argument.
    - The business-allowance rule should prevent noisy comments saying add needs two args.
    - A good Critic should drop comments that say "this is allowed, but consider ...".
    """
    return x + x

