#include <curl/curl.h>
#include <stdio.h>
#include <string.h>

struct response {
  char body[64];
  size_t used;
};

static size_t write_body(char *data, size_t size, size_t count, void *arg)
{
  struct response *reply = arg;
  size_t bytes = size * count;
  if(bytes >= sizeof(reply->body) - reply->used)
    return 0;
  memcpy(reply->body + reply->used, data, bytes);
  reply->used += bytes;
  return bytes;
}

int main(int argc, char **argv)
{
  struct response reply = {{0}, 0};
  long status = 0;
  long version = 0;
  CURLcode result;
  CURL *curl;
  if(argc != 3 || curl_global_init(CURL_GLOBAL_DEFAULT))
    return 1;
  curl = curl_easy_init();
  if(!curl)
    return 2;
  curl_easy_setopt(curl, CURLOPT_URL, argv[1]);
  curl_easy_setopt(curl, CURLOPT_CAINFO, argv[2]);
  curl_easy_setopt(curl, CURLOPT_PROXY, "");
  curl_easy_setopt(curl, CURLOPT_HTTP_VERSION, (long)CURL_HTTP_VERSION_2TLS);
  curl_easy_setopt(curl, CURLOPT_TIMEOUT, 6L);
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_body);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, &reply);
  result = curl_easy_perform(curl);
  curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
  curl_easy_getinfo(curl, CURLINFO_HTTP_VERSION, &version);
  printf("libcurl %s, status=%ld, http_version_enum=%ld, result=%d\n",
         curl_version(), status, version, (int)result);
  curl_easy_cleanup(curl);
  curl_global_cleanup();
  return result || status != 200 || version != CURL_HTTP_VERSION_2_0 ||
         strcmp(reply.body, "h2 fixture\n");
}
