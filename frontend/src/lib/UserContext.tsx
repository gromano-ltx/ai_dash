import { createContext, useContext, useState } from "react";

const UserContext = createContext<{ user: string; setUser: (u: string) => void }>({
  user: "",
  setUser: () => {},
});

export function UserProvider({ children }: { children: React.ReactNode }) {
  const [user, setUserState] = useState(() => localStorage.getItem("ai_dash_user") ?? "");
  function setUser(u: string) {
    setUserState(u);
    if (u) localStorage.setItem("ai_dash_user", u);
    else localStorage.removeItem("ai_dash_user");
  }
  return <UserContext.Provider value={{ user, setUser }}>{children}</UserContext.Provider>;
}

export const useActiveUser = () => useContext(UserContext);
