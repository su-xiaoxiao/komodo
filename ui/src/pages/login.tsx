import { LoginPage } from "mogh_ui";
import { useUserInvalidate } from "@/lib/hooks";
import LanguageSwitch from "@/localization/language-switch";

export default function Login(props: {
  passkeyIsPending?: boolean;
  totpIsPending?: boolean;
}) {
  const userInvalidate = useUserInvalidate();
  return (
    <><div style={{ position: 'fixed', right: 16, top: 16, zIndex: 200 }}><LanguageSwitch /></div><LoginPage
      {...props}
      appName="KOMODO"
      iconLink="/mogh-512x512.png"
      iconLinkAlt="moghtech"
      exampleConfigLink="https://github.com/moghtech/komodo/blob/main/config/core.config.toml"
      onLogin={userInvalidate}
    /></>
  );
}
