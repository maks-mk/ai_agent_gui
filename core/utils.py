def truncate_output(text: str, limit: int, source: str = "tool") -> str:
    """
    Truncates text to the specified limit and appends a truncation message.
    
    Args:
        text: The text to truncate.
        limit: The maximum number of characters allowed.
        source: The source of the text (e.g., 'shell', 'file', 'search').
        
    Returns:
        The truncated text with a footer if truncation occurred.
    """
    if not text:
        return text
        
    if len(text) <= limit:
        return text
    
    truncated = text[:limit]
    original_length = len(text)
    
    return f"{truncated}\n... [TRUNCATED from {original_length} chars | source={source}]"
