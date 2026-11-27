import { createRoot } from "react-dom/client";
import "../../../public/styles.css";
import "./wallet.css";
import { Wallet } from "./Wallet";

createRoot(document.getElementById("root")!).render(<Wallet />);
