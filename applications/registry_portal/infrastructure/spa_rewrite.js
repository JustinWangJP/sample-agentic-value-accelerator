function handler(event) {
  var request = event.request;
  if (request.uri === '/api' || request.uri.indexOf('/api/') === 0) {
    // body は文字列で直接指定できる（data と encoding を省くとテキストとして扱う）。
    // https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/functions-event-structure.html#functions-event-structure-response
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
