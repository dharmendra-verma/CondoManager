// container-app.bicep — Hello-world Container App (smoke-test surface).
// Jira: CM-16  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// Acts as the initial app shell so CM-16 has something to deploy. The
// hello-app image is pulled from MCR (no ACR yet — that's CM-18). Scale 0-1
// + 0.25 vCPU / 0.5 Gi keeps idle cost at zero and stays well inside the
// 180K vCPU-sec/mo Consumption free grant even if poked frequently.
//
// External ingress + targetPort 8080 (the port mcr.microsoft.com/k8s/demo/
// hello-app:1.0 listens on). transport `auto` lets the platform pick HTTP/1.1
// vs HTTP/2 based on the client.

targetScope = 'resourceGroup'

@description('Environment short name (dev or prod). Used in resource names and tags.')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Resource ID of the parent Container Apps Managed Environment.')
param environmentId string

@description('Hello-world image. MCR is used until ACR (CM-18) lands.')
param image string = 'mcr.microsoft.com/k8s/demo/hello-app:1.0'

@description('Port the hello-app container listens on.')
param targetPort int = 8080

@description('CPU cores allocated to the container. 0.25 is the Consumption-plan minimum.')
param cpu string = '0.25'

@description('Memory allocated to the container. Must be paired with cpu per the Consumption sizing table.')
param memory string = '0.5Gi'

@description('Minimum replica count. 0 means scale-to-zero when idle (no vCPU-sec spend).')
@minValue(0)
param minReplicas int = 0

@description('Maximum replica count. Capped at 1 for the hello-world smoke test.')
@minValue(1)
param maxReplicas int = 1

var containerAppName = 'ca-hello-condomanager-${env}'

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: tags
  properties: {
    environmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'hello'
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output containerAppId string = containerApp.id
output containerAppName string = containerApp.name
output fqdn string = containerApp.properties.configuration.ingress.fqdn
output latestRevisionName string = containerApp.properties.latestRevisionName
