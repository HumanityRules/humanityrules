#FROM: https://github.com/flatiron-labs/encrypt
defmodule Encrypt do
  @moduledoc """
  Documentation for Encrypt.
  """
  @aad "AES256GCM"
  @pad_length 16
  @tag_length 16

  @doc """
  `execute_action([action: "generate_secret"])`
  Generates a random base64 encoded secret key.

  `execute_action([file: path_to_file, action: "encrypt/decrypt", key: secret_key])`
  Encrypts or decrypts and base64 encodes/decodes the given file with the secret key.
  """
  def execute_action([action: "generate_secret"]) do
    :crypto.strong_rand_bytes(16)
    |> :base64.encode
  end

  def execute_action([file: file, action: action, key: key]) do
    file_contents = File.read!(file)
    apply(__MODULE__,String.to_atom(action), [file_contents, key])
    |> write_to_file(file, action)
  end

  def write_to_file(contents, file, "encrypt") do
    {:ok, file_pid } = File.open(file, [:write])
    IO.binwrite(file_pid, :base64.encode(contents))
    File.close(file_pid)
  end

  def write_to_file(contents, file, "decrypt") do
    {:ok, file_pid } = File.open(file, [:write])
    IO.binwrite(file_pid, contents)
    File.close(file_pid)
  end

  @doc """
  encrypts the given string of text with the given secret key
  """
  def encrypt(val, key) do
    iv = :crypto.strong_rand_bytes(16)
    {ciphertext, tag} =
      :crypto.crypto_one_time_aead(:aes_128_gcm, decode_key(key), iv, to_string(val), @aad, @tag_length, true)
    iv <> tag <> ciphertext
    |> Base.encode64
  end

  def encrypt_pad(val, key), do: encrypt(pad(to_string(val), @pad_length), key)

  @doc """
  decrypts the given string of text with the given secret key
  """
  def decrypt(ciphertext, key) do
    ciphertext = :base64.decode(ciphertext)
    <<iv::binary-16, tag::binary-16, ciphertext::binary>> = ciphertext
    :crypto.crypto_one_time_aead(:aes_128_gcm, decode_key(key), iv, ciphertext, @aad, tag, false)
  end

  def decrypt_pad(ciphertext, key), do: decrypt(ciphertext, key) |> unpad()

  def decode_key(key) do
    :base64.decode(key)
  end

  defp pad(data, block_size) do
    to_add = block_size - rem(byte_size(data), block_size)
    data <> :binary.copy(<<to_add>>, to_add)
  end

  defp unpad(data) do
    to_remove = :binary.last(data)
    :binary.part(data, 0, byte_size(data) - to_remove)
  end
end
