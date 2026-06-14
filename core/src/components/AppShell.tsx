import { NavLink, Outlet } from "react-router-dom";
import { FolderOpen, Package, Settings } from "lucide-react";
import { clsx } from "clsx";

const NAV = [
  { to: "/projects", label: "项目", icon: FolderOpen },
  { to: "/modules", label: "模块", icon: Package },
  { to: "/settings", label: "设置", icon: Settings },
];

export function AppShell() {
  return (
    <div className="flex h-screen">
      <aside className="w-56 bg-bg-surface border-r border-bg-muted flex flex-col">
        <div className="px-4 py-4">
          <div className="text-base font-medium">PlantOmics Studio</div>
          <div className="text-xs text-ink-faint mt-0.5">v1.0.0</div>
        </div>
        <nav className="flex-1 py-2">
          {NAV.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-2 px-4 py-2 text-sm",
                  isActive
                    ? "bg-bg-muted text-ink"
                    : "text-ink-muted hover:bg-bg-muted/50"
                )
              }
            >
              <it.icon size={16} />
              <span>{it.label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
