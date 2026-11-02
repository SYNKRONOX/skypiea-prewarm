"""Tool name -> (tool class, server, Service URL), from tools/tool_map.yaml."""

from __future__ import annotations

from dataclasses import dataclass

from tools.common.toolmap import ToolMap, k8s_name, load


@dataclass(frozen=True)
class Route:
    tool_class: str
    server: str
    url: str


class Router:
    """params.name may be a tool class (``write_file``), a server name (``edit_file``), or
    anything else; unknown names go to the fallback server as class ``other``."""

    def __init__(self, namespace: str, tool_map: ToolMap | None = None, port: int = 80,
                 url_template: str | None = None):
        self.tm = tool_map or load()
        self.ns, self.port = namespace, port
        self.url_template = url_template or "http://{svc}.{ns}.svc.cluster.local:{port}/mcp"

    def url(self, server: str) -> str:
        return self.url_template.format(svc=k8s_name(server), ns=self.ns, port=self.port,
                                        server=server)

    def resolve(self, name: str) -> Route:
        server = self.tm.server_for(name)
        if server is not None:
            cls = name
        elif name in self.tm.servers:
            server = name
            classes = self.tm.servers[name].classes
            cls = classes[0] if classes else name
        else:
            server, cls = self.tm.fallback, "other"
        return Route(cls, server, self.url(server))

    def servers(self) -> list[str]:
        return list(self.tm.servers)
