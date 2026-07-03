from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    PROCESS_ENGINEER = "process_engineer"
    QUALITY_ENGINEER = "quality_engineer"
    QUALITY_MANAGER = "quality_manager"
    FACTORY_DIRECTOR = "factory_director"
    GROUP_IT = "group_it"
    OPERATOR = "operator"


@dataclass(frozen=True)
class DataScope:
    plants: set[str]
    lines: set[str]
    processes: set[str]


class RBACPolicy:
    DEFAULT_POLICIES: dict[Role, DataScope] = {
        Role.OPERATOR: DataScope(plants={"*"}, lines={"own"}, processes={"own"}),
        Role.PROCESS_ENGINEER: DataScope(plants={"*"}, lines={"*"}, processes={"own"}),
        Role.QUALITY_ENGINEER: DataScope(plants={"*"}, lines={"*"}, processes={"*"}),
        Role.QUALITY_MANAGER: DataScope(plants={"*"}, lines={"*"}, processes={"*"}),
        Role.FACTORY_DIRECTOR: DataScope(plants={"*"}, lines={"*"}, processes={"*"}),
        Role.GROUP_IT: DataScope(plants={"*"}, lines={"*"}, processes={"*"}),
    }

    def scope_for(self, role: str) -> DataScope:
        try:
            return self.DEFAULT_POLICIES[Role(role)]
        except (KeyError, ValueError):
            return DataScope(plants=set(), lines=set(), processes=set())

    def can_read(self, role: str, plant: str, line: str, process: str) -> bool:
        s = self.scope_for(role)
        return (
            ("*" in s.plants or plant in s.plants)
            and ("*" in s.lines or line in s.lines)
            and ("*" in s.processes or process in s.processes)
        )
