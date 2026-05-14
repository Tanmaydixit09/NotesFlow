pipeline {
    agent any

    environment {
        IMAGE_NAME     = 'noteflow-app'
        IMAGE_TAG      = "${BUILD_NUMBER}"
        DOCKERHUB_USER = 'tanmaydixit09'
    }

    stages {

        stage('Checkout') {
            steps {
                echo '📥 Checking out code from GitHub...'
                checkout scm
            }
        }

        stage('Install Dependencies') {
            steps {
                echo '📦 Installing Python dependencies...'
                sh '''
                    python3 -m pip install --break-system-packages --upgrade pip
                    pip3 install --break-system-packages -r requirements.txt
                '''
            }
        }

        stage('Run Tests') {
            steps {
                echo '🧪 Running unit tests...'
                sh '''
                    pip3 install --break-system-packages pytest
                    pytest test_app.py -v --tb=short
                '''
            }
        }

        stage('Build Docker Image') {
            steps {
                echo '🐳 Building Docker image...'
                sh """
                    docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .
                    docker tag ${IMAGE_NAME}:${IMAGE_TAG} ${IMAGE_NAME}:latest
                """
            }
        }

        stage('Push to Docker Hub') {
            steps {
                echo '🚀 Pushing image to Docker Hub...'

                withCredentials([usernamePassword(
                    credentialsId: 'dockerhub-credentials',
                    usernameVariable: 'DOCKER_USER',
                    passwordVariable: 'DOCKER_PASS'
                )]) {

                    sh """
                        echo "\$DOCKER_PASS" | docker login -u "\$DOCKER_USER" --password-stdin

                        docker tag ${IMAGE_NAME}:latest ${DOCKERHUB_USER}/${IMAGE_NAME}:latest
                        docker tag ${IMAGE_NAME}:latest ${DOCKERHUB_USER}/${IMAGE_NAME}:${IMAGE_TAG}

                        docker push ${DOCKERHUB_USER}/${IMAGE_NAME}:latest
                        docker push ${DOCKERHUB_USER}/${IMAGE_NAME}:${IMAGE_TAG}
                    """
                }
            }
        }

        stage('Deploy to Kubernetes') {
            steps {
                echo '☸️ Deploying to Kubernetes...'
                sh '''
                    kubectl apply -f k8s/deployment.yaml
                    kubectl apply -f k8s/service.yaml
                    kubectl rollout status deployment/noteflow-deployment
                '''
            }
        }
    }

    post {

        success {
            echo '✅ Pipeline completed successfully! NoteFlow deployed successfully.'
        }

        failure {
            echo '❌ Pipeline failed. Check logs for details.'
        }

        always {
            echo '🧹 Cleaning up Docker images...'

            sh """
                docker rmi ${IMAGE_NAME}:${IMAGE_TAG} || true
                docker rmi ${IMAGE_NAME}:latest || true
            """
        }
    }
}
