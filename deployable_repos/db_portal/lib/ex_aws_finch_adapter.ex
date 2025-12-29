defmodule ExAwsFinchAdapter do
  def request(method, uri, body, headers, opts) do
    finch_name = Application.get_env :ex_aws, :finch_process_name
    case Finch.build(method, uri, headers, body, opts) |> Finch.request(finch_name) do
      {:ok, %{status: status}=r} -> {:ok, Map.put(r, :status_code, status)}
      r -> r
    end
  end
end
