interface QueryStreamChunk {
  type: string;
  text?: string;
}

async function runTest() {
  // Case 1: split mid-key
  const chunk1 = '{"typ';
  const chunk2 = 'e":"content", "text": "hello"}\n';
  
  // Case 2: split mid-value
  const chunk3 = '{"type":"content", "text": "wo';
  const chunk4 = 'rld"}\n';
  
  // Case 3: between two complete JSON objects with no newline
  const chunk5 = '{"type":"fast_path", "text":"fast"}{"type":"done"}\n';
  
  const chunks = [chunk1, chunk2, chunk3, chunk4, chunk5];
  
  const results: QueryStreamChunk[] = [];
  
  const mockReader = {
    i: 0,
    read: async () => {
      if (mockReader.i < chunks.length) {
        return { done: false, value: new TextEncoder().encode(chunks[mockReader.i++]) };
      }
      return { done: true, value: undefined };
    }
  };

  // Re-implement the parsing logic from api.ts
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await mockReader.read();
    if (done) {
      if (buffer.trim()) {
        try {
          results.push(JSON.parse(buffer));
        } catch {
          // ignore parsing error for trailing chunk
        }
      }
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\}\{/g, '}\n{');
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        results.push(JSON.parse(line));
      } catch {
        // ignore parsing error for chunk line
      }
    }
  }

  console.log("Parsed chunks:", results);
  
  const expectedTypes = ['content', 'content', 'fast_path', 'done']; 
  const parsedTypes = results.map(r => r.type);
  
  let passed = true;
  for(let i=0; i<expectedTypes.length; i++) {
     if(parsedTypes[i] !== expectedTypes[i]) {
        passed = false;
     }
  }

  if (passed && results.length >= 4) {
    console.log("TEST PASSED: chunks parsed successfully.");
  } else {
    console.error("TEST FAILED: expected", expectedTypes, "got", parsedTypes);
    process.exit(1);
  }
}

runTest().catch(console.error);
