"""本地代理转发器 — 接收无认证请求，转发到带认证的上游代理。

用法: python proxy_forwarder.py [local_port] [upstream_host:port:user:pass]
"""
import socket
import threading
import sys
import base64
import select


def tunnel(sock1, sock2):
    """双向数据转发"""
    sockets = [sock1, sock2]
    while True:
        try:
            readable, _, exceptional = select.select(sockets, [], sockets, 60)
            if exceptional:
                break
            for s in readable:
                data = s.recv(65536)
                if not data:
                    return
                target = sock2 if s is sock1 else sock1
                target.sendall(data)
        except:
            break
    for s in sockets:
        try:
            s.close()
        except:
            pass


def handle_client(client_sock, upstream_host, upstream_port, auth_header):
    """处理客户端连接"""
    upstream_sock = None
    try:
        client_sock.settimeout(30)
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = client_sock.recv(4096)
            if not chunk:
                client_sock.close()
                return
            request += chunk

        first_line = request.split(b"\r\n")[0].decode(errors="ignore")
        parts = first_line.split(" ")
        method = parts[0] if parts else ""

        # 连接上游代理
        upstream_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        upstream_sock.settimeout(15)
        upstream_sock.connect((upstream_host, int(upstream_port)))

        if method == "CONNECT":
            # HTTPS 隧道
            target = parts[1] if len(parts) > 1 else ""
            connect_req = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: {auth_header}\r\n\r\n"
            upstream_sock.sendall(connect_req.encode())

            # 读取上游响应
            resp = b""
            while b"\r\n\r\n" not in resp:
                data = upstream_sock.recv(4096)
                if not data:
                    client_sock.close()
                    upstream_sock.close()
                    return
                resp += data

            status_line = resp.split(b"\r\n")[0].decode(errors="ignore")
            if "200" in status_line:
                client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                tunnel(client_sock, upstream_sock)
            else:
                client_sock.sendall(resp)
        else:
            # HTTP 请求
            lines = request.split(b"\r\n")
            lines.insert(1, f"Proxy-Authorization: {auth_header}".encode())
            upstream_sock.sendall(b"\r\n".join(lines))
            while True:
                data = upstream_sock.recv(65536)
                if not data:
                    break
                client_sock.sendall(data)
    except Exception:
        pass
    finally:
        for s in [client_sock, upstream_sock]:
            if s:
                try:
                    s.close()
                except:
                    pass


def run_local_proxy(local_port, upstream_host, upstream_port, user, password):
    """运行本地代理"""
    cred = base64.b64encode(f"{user}:{password}".encode()).decode()
    auth_header = f"Basic {cred}"

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", local_port))
    server.listen(100)
    print(f"[proxy_forwarder] 本地代理 127.0.0.1:{local_port} → {upstream_host}:{upstream_port}", flush=True)

    while True:
        try:
            client_sock, addr = server.accept()
            t = threading.Thread(
                target=handle_client,
                args=(client_sock, upstream_host, upstream_port, auth_header),
                daemon=True
            )
            t.start()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[proxy_forwarder] accept error: {e}", flush=True)

    server.close()


if __name__ == "__main__":
    local_port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    upstream = sys.argv[2] if len(sys.argv) > 2 else "38.154.203.95:5863:kjcfghwu:6mm8toe8w2a5"
    parts = upstream.split(":")
    if len(parts) == 4:
        run_local_proxy(local_port, parts[0], int(parts[1]), parts[2], parts[3])
    else:
        print("格式: host:port:user:pass")
