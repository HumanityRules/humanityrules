defmodule DbPortal.VividCortex.Client do
  alias DbPortal.VividCortex.{Host,VcStat}

  @api_token "QuLPU8sKmnuXwY54BDPvtTvon59yMmPy"
  @api_addr "https://coursehero.app.vividcortex.com/api/v2/"
  def stats_for_hosts(_hosts, hour) do
    interval = time_interval(hour)
    url = url(:digest, interval)
    query_vc(url, @api_token, "host.totals.queries.time_us")
    # query VC with list of hosts for basic stats for this hour
  end

  @digest_chunk_size 50
  def stats_for_digests(digests,hour) do
    interval = time_interval(hour)
    url = url(:metrics, interval)
    digests
    |> Enum.chunk_every(@digest_chunk_size)
    |> Enum.map(&metrics_for_digests/1)
    |> Enum.map(&stats_for_metrics(&1, url))
    |> Enum.flat_map(&chunk_to_stat_map(&1, hour))
  end

  def events(event, interval) do
    url = url(:events, event, interval)
    query_vc(url, @api_token)
  end


  def stats_for_metrics(metrics, url) do
    query_vc(url, @api_token, metrics)
  end
  def stats_for_metrics(metrics, url, hosts) do
    query_vc(url, @api_token, metrics, hosts)
  end

  defp metrics_for_digests(digests) do
    digests
    |> Enum.map(&metrics_for_digest/1)
    |> Enum.join(",")
  end

  defp metrics_for_digest(digest) do
    "host.queries.q.#{digest}.time_us,host.queries.q.#{digest}.tput,host.queries.q.#{digest}.rows_examined.tput"
  end

  @metric_regex ~r|host\.queries\.q\.(?<digest>[^.]+)\.(?<stat>.*)|
  @doc ~S"""
  ## Example usage
  iex> alias DbPortal.VividCortex.Client
  iex> Client.parse_metric("host.queries.q.fffca4d67ea0a788.rows_examined.tput")
  ["fffca4d67ea0a788", "rows_examined.tput"]
  """
  def parse_metric(metric) do
    Regex.run(@metric_regex, metric, capture: :all_but_first)
  end

  def chunk_to_stat_map(chunk_results, hour) do
    chunk_results
    |> Enum.map(fn
      %{"host" =>  host, "metric" =>  metric, "series" =>  [value]} ->
        [digest, stat] = parse_metric(metric)
        stat_id = VcStat.stat_to_id(stat)
        host_id = Host.host_to_id(host)
        %{host_id: host_id, digest: digest, stat_id: stat_id, value: value, hour: hour}
    end)
  end

  def stats_for_digest(_digest, interval, metrics) do
    url = url(:digest, interval)
    # host.queries.e.006f0fdaaef3e26d.time_us
    query_vc(url,@api_token, metrics)
  end

  @epoch ~N[1970-01-01 00:00:00]
  @hour_in_seconds 60*60
  def time_interval(hour) do
    begin = NaiveDateTime.diff(hour, @epoch, :second)
    {begin, begin + @hour_in_seconds}
  end

  def url(:events, event, {from, until}=_interval) do
    #/api/v2/events?addGlobal=1&from=1609178705&levels=warn&types=Approaching+Max+MySQL+Connections&until=1609177589' \

    #api = Application.get_env(:vivid_cortex, :api) |> IO.inspect
    #api[:api_addr] <> "metrics/query-series?rank=0&samplesize=3600&separateHosts=1&from=#{from}&until=#{until}"
    @api_addr <> "events?addGlobal=1&from=#{from}&until=#{until}&types=#{event}"
  end

  def url(_type, {from, until}=_interval) do
    #api = Application.get_env(:vivid_cortex, :api) |> IO.inspect
    #api[:api_addr] <> "metrics/query-series?rank=0&samplesize=3600&separateHosts=1&from=#{from}&until=#{until}"
    @api_addr <> "metrics/query-series?rank=0&samplesize=3600&separateHosts=1&from=#{from}&until=#{until}"
  end


  def query_vc(url, api_token) do
    b = make_request(url, api_token, make_req_body([]))
    res = Jason.decode!(b)
    %{"data"=> events} = res
    events
  end

  def query_vc(url, api_token, metrics, hosts \\ [44,45,46]) do
    req_body = make_req_body(%{metrics: metrics}, hosts)
    b = make_request(url, api_token, req_body)
    # IO.inspect Jason.decode!(b)

    %{"data"=>metric_results } = Jason.decode!(b)
    for %{"elements"=> elements} <- metric_results do
      # IO.inspect elements
      for %{"host"=> _host, "metric"=> _metric, "series" =>_series}=r <- elements do
        r
      end
    end
    |> List.flatten
  end

  defp make_request(url, api_token, body) do
    Finch.start_link(name: MyFinch)
    {:ok, %Finch.Response{body: b}} = Finch.request(MyFinch, :post, url, [
        {"Authorization", "Bearer #{api_token}" },
        {"authority", "coursehero.app.vividcortex.com"},
        { "x-vc-payload-query-string", "1" },

        {"Accept", "application/json, */*" },
        {"content-type", "application/json;charset=UTF-8" }
      ],
      body
    )
    b
  end

  def make_req_body(kvs, hosts \\ [44,45,46]) do
    hosts_str = ~s|"host":[#{Enum.join(hosts,",")}]|
    kv_str = [hosts_str | Enum.map(kvs, fn({k,v})-> ~s|"#{k}":"#{v}"| end)]
             |> Enum.join(",")
    "{#{kv_str}}"
  end


end

#
# %{
#   "data" => [
#     %{
#       "elements" => [
#         %{
#           "metric" => "host.totals.queries.time_us",
#           "rank" => 1,
#           "series" => [17048048.091906838, 25728219.66122706,
#            26083047.457489643, 46054783.41855515, 53708925.88079091,
#            46916508.066621415, 56091341.084268264, 72971006.24933125,
#            71298246.63837907, 75911007.98979266, 68915831.4349017,
#            67243071.82394952, 66786864.65732619, 71044798.2124772,
#            67192382.13876915, 63238586.69470034, 66584105.91660471,
#            64607208.1945703, 65468932.84263658, 61058930.23194446,
#            52340304.38092094, 24308908.47617672, 25119943.43906263,
#            21064768.624633085, 20405802.717288285, 20304423.346927546,
#            21368906.735715304, 22838907.605946008, 23954080.679914135,
#            25728219.661227055, 25069253.753882263, 25930978.401948534,
#            30594429.43854251, 43114781.67809373, 42354436.40038819,
#            46460300.8999981, 47474094.60360549, 49856509.80708284,
#            46409611.21481774, 42759953.88183114, 52086855.955019094,
#            53607546.51043017, 53810305.25115165, ...]
#         }
#       ],
#       "total" => 1
#     }
#   ]
# }
# 

# curl 'https://coursehero.app.vividcortex.com/api/v2/metrics/query-series?from=1589784840&rank=0&samplesize=840&separateHosts=0&until=1590033480' \
#   --data-binary '{"metrics":"host.totals.queries.time_us","host":[0,41,42,43]}' \
#   --compressed \
# -H 'Authorization: Bearer QuLPU8sKmnuXwY54BDPvtTvon59yMmPy' \
# -H 'Accept: application/json, */*' \
#   -H 'content-type: application/json;charset=UTF-8' \
# -H 'X-Indent: true'
#
#
# 'https://coursehero.app.vividcortex.com/api/v2/metrics/query-series?from=1589784840&rank=0&separateHosts=0&until=1590033480
#end
#
#
#
# curl 'https://coursehero.app.vividcortex.com/api/v2/metrics/query-series?from=1592011570&samplesize=2085&until=1592616370' \
#   -H 'authority: coursehero.app.vividcortex.com' \
#   -H 'accept: application/json, text/plain, */*' \
#   -H 'x-xsrf-token: IWiIug6qHCPWZ-0S7cMMcvQrslWc5lIi2TSXXroXdUQ' \
#   -H 'authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6MCwidXNlciI6MzI0OTYsImhhc2giOiIiLCJlbnYiOjI3OTAsIm9yZyI6MTc3OCwibmFtZSI6IiIsInN0YXR1cyI6MCwiZXhwaXJlcyI6MTU5MjYxODE3MCwiY3JlYXRlZCI6MTU5MjYxNjM3MCwidXBkYXRlZCI6MTU5MjYxNjM3MCwiZGVsZXRlZCI6MCwidHlwZSI6InVzZXIiLCJyb2xlIjp7ImlkIjowLCJuYW1lIjoiIn0sInNlc3Npb24iOiJ3ZWJhcHBfc2Vzc19WbHcyS1Fha241bEVaNkl1aVF5ekdIdjR2YVJnQmcwMjFQczYtV3ZTIiwiYWN0aW9ucyI6WyJlbnY6cmVhZCIsImVudjp3cml0ZSIsImVudjpzZXR0aW5nczpyZWFkIiwiZW52OnNldHRpbmdzOndyaXRlIiwiZW52OnNhbXBsZXM6cmVhZCIsIm9yZzp1c2VyOnJlYWQiLCJhY2N0OmxpY2Vuc2VzOnJlYWQiLCJhY2N0OmxpY2Vuc2VzOndyaXRlIiwiZW52OmNyZWRlbnRpYWxzOnJlYWQiLCJlbnY6Y3JlZGVudGlhbHM6d3JpdGUiLCJlbnY6dG9rZW46Y3JlYXRlIiwiZW52OnRva2VuOnJlYWQiLCJlbnY6dGVhbTphZGQiLCJvcmc6ZW52OmNyZWF0ZSIsIm9yZzp0ZWFtOnVwZGF0ZSIsImFjY3Q6b3duZXI6dXBkYXRlIiwiYWNjdDpjYW5jZWwiLCJhY2N0OmF1dGg6dXBkYXRlIiwib3JnOnRlYW06cmVhZCIsIm9yZzpjb25maWc6dXBkYXRlIl19.6XCZ0tRmcIP1l0WpmjrB21_5gU8GjnBz_H2V9owQuXwQHPqXQt6VqVk56PKeGntLeBEDK61V9GQH_zDjRQaOozH3tyarKa5stZP6GIBwDRKQURhJcubkOHu9H8SnV5ryZ9ruq6Gr9RjHKHFwz704BPPe_DanVgNXmT4SeD3qqoM-G_DBp6tSMhEpXDkB_QvLHhttz2Q_duDDbbxxLh5xOM4cPUf2e-AL_EoyEtw91FU6PG1hEGkO4ml7Gg9Ehv7p5NeMECLvEzuxkoL1AzWPhbGHFSIjmR_sbPzEeK7jnLoiM3t9BPaQV8aswG1A7rjlfMxepzxjDrhUNRWbmEFy3Q' \
#   -H 'x-vc-payload-query-string: 1' \
#   -H 'user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.113 Safari/537.36' \
#   -H 'content-type: application/json;charset=UTF-8' \
#   -H 'origin: https://coursehero.app.vividcortex.com' \
#   -H 'sec-fetch-site: same-origin' \
#   -H 'sec-fetch-mode: cors' \
#   -H 'sec-fetch-dest: empty' \
#   -H 'referer: https://coursehero.app.vividcortex.com/Prod/queries/b10e35dc4ea44b15?explainTab=JSON&from=-604800&hosts=&selectedGraph=Total%20Time&showSamples&until=0&eventTypes=Database%20Server%20Restart' \
#   -H 'accept-language: en-US,en;q=0.9' \
#   -H 'cookie: vco=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.W3siY291cnNlaGVybyI6eyJlbnZzIjpbXSwibGFzdEVudiI6bnVsbH19LCJjb3Vyc2VoZXJvIl0.g4yKdeQLhZyx2KoWJIyCvcFXvi7uSBHh87eze_xJQQs; _ga=GA1.4.1209577876.1583788377; _fbp=fb.1.1584559351743.216399123; __hs_opt_out=no; hubspotutk=62b84295b78977d49a75eb57c32d1a26; __hssrc=1; _ga=GA1.2.1690281973.1587746819; __hstc=40586429.62b84295b78977d49a75eb57c32d1a26.1584628387873.1591636547141.1592586895339.20; PHPSESSID=Vlw2KQakn5lEZ6IuiQyzGHv4vaRgBg021Ps6-WvS; REMEMBERME=QXBwXE1vZGVsXEVudGl0eVxPcmdVc2VyOmNtbGphQzVzYVdWaWJHbHVaMEJqYjNWeWMyVm9aWEp2TG1OdmJRPT06MTU5NTE3OTIyOTo2MzU4ZGU1NmJkMDBiZjlhODMzMjk3ZGVmOTlmY2YyMzIxMzkwMDUxOWQwMzY4ZTU5ZGRlZmMzODZmM2ZkNTVl; _gid=GA1.4.817386954.1592587237; XSRF-TOKEN=IWiIug6qHCPWZ-0S7cMMcvQrslWc5lIi2TSXXroXdUQ; intercom-session-704c9e2a2a2877207ecc28c1990b0342756f9f35=R3pvTGFROWZvWEZ3Q25CaTc5MlY2bnJCUVdpeU93L3Y4b0U5cTlLdEdpNGQyMlZXM21QVC8yWmRWSUFkTUgrSi0tSGlyTm5OZHRyT2VwSFJzdTdOTzN3QT09--24634330b8f13861f735fdf982709bb80cfe2202; _gat_UA-34462605-5=1' \
#   --data-binary '{"metrics":"host.queries.q|p|e|c|f.b10e35dc4ea44b15.time_us,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.warnings.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.errors.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.affected_rows.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.rows_sent.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.no_index.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.slow.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.p99_latency_us,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.created_tmp_tables.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.created_tmp_disk_tables.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.lock_time_us,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.select_full_join.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.rows_examined.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.sort_scan.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.sort_rows.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.select_scan.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.shared_blks_hit.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.shared_blks_read.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.shared_blks_dirtied.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.shared_blks_written.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.local_blks_hit.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.local_blks_read.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.local_blks_dirtied.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.local_blks_written.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.temp_blks_read.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.temp_blks_written.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.blk_read_time.tput,
# host.queries.q|p|e|c|f.b10e35dc4ea44b15.blk_write_time.tput",
# "host":[0,
# 41,
# 42,
# 43]}' \
#   --compressed
