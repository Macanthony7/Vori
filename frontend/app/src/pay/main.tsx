import { createRoot } from "react-dom/client";
import "../../../public/styles.css";
import "./pay.css";
import { Checkout } from "./Checkout";

// Served at /pay/{id}
const id = decodeURIComponent(location.pathname.split("/")[2] || "");
createRoot(document.getElementById("root")!).render(<Checkout id={id} />);
