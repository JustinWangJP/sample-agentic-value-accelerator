function handler(event) {
  var request = event.request;
  if (request.uri === '/api' || request.uri.indexOf('/api/') === 0) {
    return {
      statusCode: 404,
      statusDescription: 'Not Found',
      headers: { 'content-type': { value: 'application/json' } },
      body: JSON.stringify({ error: { message: 'API は専用の origin を使用してください。' } })
    };
  }
  var lastSegment = request.uri.substring(request.uri.lastIndexOf('/') + 1);
  if ((request.method === 'GET' || request.method === 'HEAD') && lastSegment.indexOf('.') === -1) {
    request.uri = '/index.html';
  }
  return request;
}
