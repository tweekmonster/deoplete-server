from deoplete import server


class Whatevs(server.BaseServer):
    pass


if __name__ == "__main__":
    server.run_server(Whatevs)
