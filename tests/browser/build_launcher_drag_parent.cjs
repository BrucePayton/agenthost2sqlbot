// Build the actual Davinci launcher and bridge against fixture authentication.
const fs = require('node:fs/promises')
const path = require('node:path')
const esbuild = require('esbuild')

async function main() {
  const [davinciRoot, outfile] = process.argv.slice(2)
  const webapp = path.join(davinciRoot, 'webapp')
  const less = require(path.join(webapp, 'node_modules/less'))
  const entry = `
    import React, {useCallback, useRef, useState} from 'react';
    import ReactDOM from 'react-dom';
    import AgentLauncher from ${JSON.stringify(path.join(webapp, 'share/containers/WorkBenchNew/agent/launcher/index.tsx'))};
    import {createParentBridgeV2} from ${JSON.stringify(path.join(webapp, 'share/containers/WorkBenchNew/agent/bridge/createParentBridgeV2.ts'))};
    import {CONTRACT_DIGEST, CONTRACT_VERSION, PROTOCOL_VERSION} from ${JSON.stringify(path.join(webapp, 'share/containers/WorkBenchNew/agent/contracts/generated-v2.ts'))};
    window.__contract = {contractDigest: CONTRACT_DIGEST, contractVersion: CONTRACT_VERSION, protocolVersion: PROTOCOL_VERSION};
    window.panelCommands = [];
    function Parent() {
      const [open, setOpen] = useState(true);
      const [size, setSize] = useState('small');
      const drag = useRef(null);
      const bridge = useRef(null);
      const registerPanelDrag = useCallback(handler => { drag.current = handler; return () => { drag.current = null; }; }, []);
      const onIframeElement = useCallback((frame, allowedOrigin) => {
        bridge.current?.destroy();
        if (!frame || !allowedOrigin) return;
        bridge.current = createParentBridgeV2({
          allowedOrigin, getIframeWindow: () => frame.contentWindow,
          getRuntimeContext: () => window.__runtimeContext,
          subscribeRuntimeContext: () => () => {},
          executeTool: async () => ({status:'success',data:{},issues:[]}),
          onPanelCommand: command => {
            window.panelCommands.push(command);
            if (command.action === 'drag') drag.current?.(command);
            if (command.action === 'close') setOpen(false);
            if (command.action === 'resize') setSize(command.size);
          }
        });
      }, []);
      return <AgentLauncher actorObId="fixture-user" open={open} size={size}
        onToggle={() => setOpen(value => !value)} registerPanelDrag={registerPanelDrag}
        onLauncherMove={launcher => bridge.current?.sendPanelState({launcher})}
        onIframeElement={onIframeElement} />;
    }
    ReactDOM.render(<Parent />, document.getElementById('root'));
  `
  await esbuild.build({
    stdin: {contents: entry, loader: 'tsx', resolveDir: webapp}, outfile,
    bundle: true, platform: 'browser', define: {'process.env.NODE_ENV': '"test"'},
    plugins: [{name: 'fixture-styles-and-token', setup(build) {
      build.onResolve({filter: /^utils\/request$/}, () => ({path: 'token', namespace: 'fixture'}))
      build.onLoad({filter: /.*/, namespace: 'fixture'}, () => ({contents: 'export const getToken = () => "fixture-token"'}))
      build.onLoad({filter: /\.less$/}, async args => {
        const source = await fs.readFile(args.path, 'utf8')
        const {css} = await less.render(source, {filename: args.path})
        const classes = Object.fromEntries([...source.matchAll(/\.([a-zA-Z][\w-]*)/g)].map(match => [match[1], match[1]]))
        return {loader: 'js', contents: `const style = document.createElement('style'); style.textContent = ${JSON.stringify(css)}; document.head.append(style); export default ${JSON.stringify(classes)}`}
      })
    }}]
  })
}

main().catch(error => { process.stderr.write(String(error)); process.exitCode = 1 })
