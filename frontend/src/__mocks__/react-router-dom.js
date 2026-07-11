/**
 * Jest manual mock for react-router-dom v7. The shipped package uses
 * the `exports` field which the default Jest resolver in CRA can't
 * unpack — the app itself works because webpack understands `exports`.
 * This stub gives us just enough of the API for isolated tests that
 * don't need real routing.
 */
const React = require("react");

module.exports = {
  useNavigate: () => () => {},
  useLocation: () => ({ pathname: "/", search: "", hash: "", state: null }),
  useParams: () => ({}),
  useSearchParams: () => [new URLSearchParams(), () => {}],
  Link: ({ children, ...rest }) => React.createElement("a", rest, children),
  NavLink: ({ children, ...rest }) => React.createElement("a", rest, children),
  MemoryRouter: ({ children }) => React.createElement(React.Fragment, null, children),
  BrowserRouter: ({ children }) => React.createElement(React.Fragment, null, children),
  Routes: ({ children }) => React.createElement(React.Fragment, null, children),
  Route: () => null,
  Outlet: () => null,
  Navigate: () => null,
};
